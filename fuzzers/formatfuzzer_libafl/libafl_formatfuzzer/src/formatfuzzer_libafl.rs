use std::{borrow::Cow, marker::PhantomData};

use libafl::{
    corpus::Corpus,
    inputs::{BytesInput, HasMutatorBytes, HasTargetBytes, UsesInput},
    mutators::{self, havoc_mutations, I2SRandReplace, MutationResult, Mutator},
    schedulers::{testcase_score::CorpusPowerTestcaseScore, TestcaseScore},
    stages::Stage,
    state::{HasCorpus, HasCurrentTestcase, HasRand, State, UsesState},
    Evaluator, HasMetadata,
};
use libafl_bolts::{
    impl_serdeany,
    tuples::{tuple_list, tuple_list_type, Map, MappingFunctor, Merge},
    Error, Named,
};
use serde::{Deserialize, Serialize};

use crate::formatfuzzer_wrapper::{DecisionSeed, FileHandle, InputData};

use super::formatfuzzer_wrapper::FormatFuzzer;

#[derive(Debug, Default, Serialize, Deserialize, Clone)]
pub struct FormatFuzzerMetadata {
    #[serde(skip)]
    handle: Option<FileHandle>,
    validity: f32,
    decisionseed: Vec<u8>,
}
impl_serdeany!(FormatFuzzerMetadata);

pub struct DecisionSeedWrapper<'a, 'b, T>(T, &'a FormatFuzzer<'b>);

impl<'a, 'b, T: Named> DecisionSeedWrapper<'a, 'b, T> {
    fn to_seed(&self, input: &[u8]) -> Vec<u8> {
        match self.1.parse(InputData(input.into())) {
            Ok(x) => x.0.to_vec(),
            Err(x) => x.0.to_vec(),
        }
    }
    fn to_input(&self, seed: &[u8]) -> Vec<u8> {
        self.1.generate(DecisionSeed(seed.into())).0.to_vec()
    }
}

impl<'a, 'b, T: Named, S> Mutator<BytesInput, S> for DecisionSeedWrapper<'a, 'b, T>
where
    for<'c> T: Mutator<BytesInput, S>,
    S: State + HasMetadata,
{
    fn mutate(&mut self, state: &mut S, input: &mut BytesInput) -> Result<MutationResult, Error> {
        tracy_full::zone!("DecisionSeedWrapper::mutate");
        // let mapped = &mut (self.mapper)(input);
        let mut decision_seed = BytesInput::new(self.to_seed(input.bytes()));
        match self.0.mutate(state, &mut decision_seed) {
            Ok(MutationResult::Mutated) => {
                input.drain(..);
                input.extend(&self.to_input(decision_seed.bytes()));
                Ok(MutationResult::Mutated)
            }
            res => res,
        }
    }
}

impl<T: Named> Named for DecisionSeedWrapper<'_, '_, T> {
    fn name(&self) -> &Cow<'static, str> {
        self.0.name()
    }
}

struct DecisionSeedMapper<'a, 'b>(&'a FormatFuzzer<'b>);
impl<'a, 'b, T> MappingFunctor<T> for DecisionSeedMapper<'a, 'b> {
    type Output = DecisionSeedWrapper<'a, 'b, T>;

    fn apply(&mut self, from: T) -> Self::Output {
        DecisionSeedWrapper(from, self.0)
    }
}

type DecisionSeedHavocMutations<'a, 'b> = tuple_list_type!(
    DecisionSeedWrapper<'a, 'b, I2SRandReplace>,
    DecisionSeedWrapper<'a, 'b, mutators::BitFlipMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::ByteFlipMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::ByteIncMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::ByteDecMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::ByteNegMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::ByteRandMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::ByteAddMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::WordAddMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::DwordAddMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::QwordAddMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::ByteInterestingMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::WordInterestingMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::DwordInterestingMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesDeleteMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesDeleteMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesDeleteMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesDeleteMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesExpandMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesInsertMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesRandInsertMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesSetMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesRandSetMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesCopyMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesInsertCopyMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::BytesSwapMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::CrossoverInsertMutator>,
    DecisionSeedWrapper<'a, 'b, mutators::CrossoverReplaceMutator>,
);

pub fn decision_seed_mutations<'a, 'b>(
    ff: &'a FormatFuzzer<'b>,
) -> DecisionSeedHavocMutations<'a, 'b> {
    tuple_list!(I2SRandReplace)
        .map(DecisionSeedMapper(ff))
        .merge(
            // tuple_list_type!(DecisionSeedWrapper<I2SRandReplace>) {
            havoc_mutations().map(DecisionSeedMapper(ff)),
        )
    // tuple_list!(DecisionSeedWrapper(I2SRandReplace))
}

#[derive(Debug)]
pub struct OneSmartMutator<'a, 'b>(&'a FormatFuzzer<'b>);

impl<I, S> Mutator<I, S> for OneSmartMutator<'_, '_>
where
    S: HasRand + HasMetadata,
    I: HasMutatorBytes,
{
    fn mutate(&mut self, state: &mut S, input: &mut I) -> Result<MutationResult, Error> {
        if !state.has_metadata::<FormatFuzzerMetadata>() {
            dbg!("No metadata found?");
            return Ok(MutationResult::Skipped);
        }

        tracy_full::zone!("OneSmartMutator::mutate");

        let fh = state
            .metadata::<FormatFuzzerMetadata>()
            .unwrap()
            .handle
            .as_ref()
            .unwrap();

        let data = self.0.one_smart_mutation(fh);
        input.drain(..);
        input.extend(data.0.as_ref());
        Ok(MutationResult::Mutated)
    }
}

impl Named for OneSmartMutator<'_, '_> {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("OneSmartMutator");
        &NAME
    }
}

pub fn smart_mutations<'a, 'b>(
    ff: &'a FormatFuzzer<'b>,
) -> tuple_list_type!(OneSmartMutator<'a, 'b>) {
    tuple_list!(OneSmartMutator(ff))
}

#[derive(Debug)]
pub struct FormatFuzzerProcessStage<'a, 'b, S> {
    phantom: PhantomData<S>,
    formatfuzzer: &'a FormatFuzzer<'b>,
}

impl<'a, 'b, S> FormatFuzzerProcessStage<'a, 'b, S> {
    /// Create a new instance of the string identification stage
    #[must_use]
    pub fn new(formatfuzzer: &'a FormatFuzzer<'b>) -> Self {
        Self {
            phantom: PhantomData,
            formatfuzzer,
        }
    }

    fn add_metadata_to_current_testcase(&mut self, state: &mut S) -> Result<FileHandle, Error>
    where
        S: HasCurrentTestcase,
        <S::Corpus as Corpus>::Input: HasTargetBytes,
    {
        tracy_full::zone!("add_metadata_to_current_testcase");
        let mut tc = state.current_testcase_mut()?;
        if let Ok(md) = tc.metadata::<FormatFuzzerMetadata>() {
            if let Some(handle) = md.handle.as_ref() {
                return Ok(handle.clone()); // skip recompute
            }
        }

        let input = tc.load_input(state.corpus())?;

        let bytes = input.target_bytes();
        let handle = self.formatfuzzer.process_file(&bytes);
        let decisionseed = self.formatfuzzer.parse(InputData((&*bytes).into()));
        let decisionseed = match decisionseed {
            Ok(x) => x,
            Err(x) => x,
        };
        let decisionseed = decisionseed.0.to_vec();
        tc.add_metadata(FormatFuzzerMetadata {
            decisionseed,
            validity: handle.validity,
            handle: Some(handle.clone()),
        });

        Ok(handle)
    }
}

impl<S: State> UsesState for FormatFuzzerProcessStage<'_, '_, S> {
    type State = S;
}

impl<S, E, EM, Z> Stage<E, EM, Z> for FormatFuzzerProcessStage<'_, '_, S>
where
    S: HasCorpus + State + HasCurrentTestcase + HasMetadata + UsesInput<Input = BytesInput>,
    S::Corpus: Corpus<Input = BytesInput>,
    E: UsesState<State = S>,
    EM: UsesState<State = S>,
    Z: UsesState<State = S> + Evaluator<E, EM, State = S>,
{
    fn perform(
        &mut self,
        fuzzer: &mut Z,
        executor: &mut E,
        state: &mut Self::State,
        manager: &mut EM,
    ) -> Result<(), Error> {
        tracy_full::zone!("FormatFuzzerProcessStage::perform");
        let handle = self.add_metadata_to_current_testcase(state)?;

        let iterations = {
            let mut testcase = state.current_testcase_mut()?;
            CorpusPowerTestcaseScore::compute(state, &mut testcase)? as usize
        };

        // TODO: run smart mutations
        for _ in 0..iterations {
            let input = self.formatfuzzer.one_smart_mutation(&handle);
            // let input = self.formatfuzzer.generate(seed);
            let input = BytesInput::new(input.0.to_vec());
            let (_res, _corpus_id) = fuzzer.evaluate_input(state, executor, manager, input)?;

            // self.mutator_mut().post_exec(state, corpus_id)?;
            // post.post_exec(state, corpus_id)?;
        }

        // TODO: run non-smart mutations? i.e. i2s replace on decision seed

        Ok(())
    }

    #[inline]
    fn should_restart(&mut self, _state: &mut Self::State) -> Result<bool, Error> {
        // Stage does not run the target. No reset helper needed.
        Ok(true)
    }

    #[inline]
    fn clear_progress(&mut self, _state: &mut Self::State) -> Result<(), Error> {
        // Stage does not run the target. No reset helper needed.
        Ok(())
    }
}
